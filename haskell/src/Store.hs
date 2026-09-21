{-# LANGUAGE OverloadedStrings #-}
module Store (Store, StepMode(..), withStore, create, inspect) where

import Control.Concurrent.Async
import Control.Concurrent.MVar
import Control.Concurrent.STM
import Control.Exception
import Control.Monad
import Data.IORef
import qualified Data.Aeson as A
import qualified Data.Text as T
import qualified Database.SQLite3 as S
import Foreign.C.Types
import Model

foreign import ccall unsafe "bench_sqlite_configure" configure :: IO CInt

data Store = Store (TBQueue (Maybe (NewPost, MVar (Either SomeException Post))))
data StepMode = Safe | NoCallback | Hybrid deriving (Eq, Show)
data Connection = Connection S.Database S.Statement S.Statement S.Statement S.Statement S.Statement (IORef Bool)

open :: FilePath -> IO Connection
open path = do
  rc <- configure
  unless (rc == 0) $ fail ("SQLite configuration failed: " ++ show rc)
  bracketOnError (S.open2 (T.pack path) [S.SQLOpenReadWrite, S.SQLOpenNoMutex] S.SQLVFSDefault) S.close $ \db -> do
    S.exec db "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=10000; PRAGMA cache_size=-2000; PRAGMA wal_autocheckpoint=1000; PRAGMA temp_store=MEMORY; PRAGMA mmap_size=0; PRAGMA optimize=0x10002;"
    statements <- newIORef []
    let prepare sql = do
          stmt <- S.prepare db sql
          modifyIORef' statements (stmt :)
          pure stmt
    (Connection db <$> prepare "BEGIN IMMEDIATE"
      <*> prepare "INSERT OR IGNORE INTO users (email) VALUES (?)"
      <*> prepare "INSERT INTO posts (content, user_id) SELECT ?, id FROM users WHERE email IS ? RETURNING id, user_id, content, created_at, updated_at"
      <*> prepare "COMMIT" <*> prepare "ROLLBACK" <*> newIORef False)
      `onException` (readIORef statements >>= mapM_ S.finalize)

close :: Connection -> IO ()
close (Connection db begin user post commit rollback _) =
  mapM_ S.finalize [begin, user, post, commit, rollback] `finally`
    (S.exec db "PRAGMA optimize" `finally` S.close db)

execute :: S.Statement -> IO ()
execute = executeWith S.step

transaction :: StepMode -> Connection -> NewPost -> IO Post
transaction mode (Connection _ begin user post commit rollback failed) request = mask_ $ do
  let step = if mode == Safe then S.step else S.stepNoCB
      control = if mode == NoCallback then executeWith S.stepNoCB else execute
      executeInsert = executeWith step
  poisoned <- readIORef failed
  when poisoned $ fail "SQLite unavailable after rollback failure"
  control begin
  let cleanup = do
        -- reset can report the preceding step error, but still resets the cursor.
        forM_ [user, post, commit] $ \stmt -> do
          S.reset stmt `catch` (\(_ :: S.SQLError) -> pure ())
          S.clearBindings stmt
        control rollback `onException` writeIORef failed True
  (do
    S.bindText user 1 (email request)
    executeInsert user
    S.bindText post 1 (content request)
    S.bindText post 2 (email request)
    result <- step post
    unless (result == S.Row) $ fail "post insert returned no row"
    row <- Post <$> S.columnInt64 post 0 <*> S.columnInt64 post 1 <*> S.columnText post 2
                <*> S.columnInt64 post 3 <*> S.columnInt64 post 4
    executeInsert post
    control commit
    pure row) `onException` cleanup

-- The writer owns initialization, all SQLite calls, and finalization.
-- NoCallback is direct-sqlite's documented fast API for SQL without Haskell
-- callbacks. A step holds its GHC capability (and delays GC) until it returns;
-- Safe/Hybrid remain available to reproduce the FFI tradeoff under contention.
-- Every request gets its own reply immediately after its commit.
withStore :: Bool -> StepMode -> FilePath -> (Store -> IO a) -> IO a
withStore bound mode path action = do
  queue <- newTBQueueIO 128
  ready <- newEmptyMVar
  let loop connection = do
        next <- atomically (readTBQueue queue)
        case next of
          Nothing -> pure ()
          Just (request, reply) -> do
            result <- try (transaction mode connection request)
            putMVar reply result
            loop connection
  let launch = if bound then withAsyncBound else withAsync
  launch (bracket (open path) close $ \connection -> putMVar ready () >> loop connection) $ \worker -> do
    link worker
    takeMVar ready
    action (Store queue) `finally` (atomically (writeTBQueue queue Nothing) >> wait worker)

create :: Store -> NewPost -> IO (Either SomeException Post)
create (Store queue) request = do
  reply <- newEmptyMVar
  atomically (writeTBQueue queue (Just (request, reply)))
  takeMVar reply

executeWith :: (S.Statement -> IO S.StepResult) -> S.Statement -> IO ()
executeWith step stmt = do
  result <- step stmt `finally` (S.reset stmt `finally` S.clearBindings stmt)
  unless (result == S.Done) $ fail "statement unexpectedly returned a row"

inspect :: FilePath -> IO A.Value
inspect path = bracket (open path) close $ \(Connection db _ _ _ _ _ _) -> do
  let query sql = bracket (S.prepare db sql) S.finalize $ \stmt ->
        let loop = S.step stmt >>= \result -> case result of
              S.Done -> pure []
              S.Row -> (:) <$> S.columnText stmt 0 <*> loop
        in loop
  version <- query "SELECT sqlite_version()"
  options <- query "PRAGMA compile_options"
  pragmas <- forM ["journal_mode", "synchronous", "foreign_keys", "busy_timeout", "cache_size", "wal_autocheckpoint", "temp_store", "mmap_size"] $ \name -> do
    values <- query ("PRAGMA " <> name)
    pure (name, values)
  pure $ A.object ["version" A..= version, "compile_options" A..= options, "pragmas" A..= pragmas]
