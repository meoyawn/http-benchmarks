{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE CPP #-}
module Main where

import Control.Concurrent (forkOnWithUnmask, getNumCapabilities)
#ifdef COMPARISONS
import Control.Concurrent (myThreadId)
#endif
import Control.Exception
import Control.Monad (unless, when)
#ifdef COMPARISONS
import Control.Monad.IO.Class (liftIO)
#endif
import qualified Data.Aeson as A
import qualified Data.ByteString as B
import qualified Data.ByteString.Char8 as BC
import qualified Data.ByteString.Lazy as L
import Data.IORef
import Network.HTTP.Types
import qualified Network.Socket as N
import qualified Network.Wai as W
import qualified Network.Wai.Handler.Warp as Warp
#ifdef COMPARISONS
import qualified Snap.Core as Snap
import qualified Snap.Http.Server as Snap
#endif
import System.Directory (removeFile)
import System.Environment (getArgs)
import System.IO
import System.IO.Error (isDoesNotExistError)
import System.Posix.Files (getSymbolicLinkStatus)
import System.Posix.Signals
import Codec
import Model
import qualified Store

data Options = Options { database :: FilePath, socketPath :: FilePath, codec :: Codec, http :: String, diagnostic :: Bool, pinned :: Bool, boundWriter :: Bool, stepMode :: Store.StepMode }

parse :: Options -> [String] -> IO Options
parse opts [] = pure opts
parse opts ("-db":value:rest) = parse opts {database = value} rest
parse opts ("-socket":value:rest) = parse opts {socketPath = value} rest
parse opts ("-codec":"aeson":rest) = parse opts {codec = Aeson} rest
parse opts ("-codec":"jsonifier":rest) = parse opts {codec = Jsonifier} rest
parse opts ("-http":value:rest) | value `elem` ["warp", "snap"] = parse opts {http = value} rest
parse opts ("-inspect":rest) = parse opts {diagnostic = True} rest
parse opts ("-fork":"pinned":rest) = parse opts {pinned = True} rest
parse opts ("-fork":"default":rest) = parse opts {pinned = False} rest
parse opts ("-writer":"bound":rest) = parse opts {boundWriter = True} rest
parse opts ("-writer":"unbound":rest) = parse opts {boundWriter = False} rest
parse opts ("-sqlite-step":"safe":rest) = parse opts {stepMode = Store.Safe} rest
parse opts ("-sqlite-step":"no-callback":rest) = parse opts {stepMode = Store.NoCallback} rest
parse opts ("-sqlite-step":"hybrid":rest) = parse opts {stepMode = Store.Hybrid} rest
parse _ args = fail ("invalid arguments: " ++ show args)

handle :: Codec -> Store.Store -> B.ByteString -> B.ByteString -> B.ByteString -> IO (Status, L.ByteString)
handle encoding store method path body
  | path /= "/echo" && path /= "/posts" = pure (status404, A.encode ["not found" :: String])
  | method /= "POST" = pure (status405, A.encode ["method must be POST" :: String])
  | otherwise = case Codec.decode body of
      Left _ -> pure (status400, A.encode ["invalid JSON body" :: String])
      Right request
        | path == "/echo" -> pure (status200, encodeEcho encoding request)
        | errors@(_:_) <- validate request -> pure (status400, A.encode errors)
        | otherwise -> do
            result <- Store.create store request
            case result of
              Right post -> pure (status201, encodePost encoding post)
              Left err -> hPrint stderr err >> pure (status500, A.encode ["database error" :: String])

headers :: Status -> L.ByteString -> ResponseHeaders
headers status body = [(hContentType, "application/json"), (hContentLength, BC.pack (show (L.length body)))] ++
  [(hAllow, "POST") | status == status405]

app :: Codec -> Store.Store -> W.Application
app encoding store request respond = do
  body <- W.strictRequestBody request
  (status, result) <- Main.handle encoding store (W.requestMethod request) (W.rawPathInfo request) (L.toStrict body)
  respond (W.responseLBS status (headers status result) result)

#ifdef COMPARISONS
snapApp :: Codec -> Store.Store -> Snap.Snap ()
snapApp encoding store = do
  request <- Snap.getRequest
  body <- Snap.readRequestBody (1024 * 1024)
  let method = BC.pack (show (Snap.rqMethod request))
      path = B.takeWhile (/= 63) (Snap.rqURI request)
  (status, result) <- liftIO $ Main.handle encoding store method path (L.toStrict body)
  Snap.modifyResponse (Snap.setResponseCode (statusCode status))
  mapM_ (\(key, value) -> Snap.modifyResponse (Snap.setHeader key value)) (headers status result)
  Snap.modifyResponse (Snap.setContentLength (fromIntegral (L.length result)))
  Snap.writeLBS result
#endif

assertVacant :: FilePath -> IO ()
assertVacant path = do
  when (null path) $ fail "socket path must not be empty"
  exists <- (getSymbolicLinkStatus path >> pure True) `catch` \err ->
    if isDoesNotExistError err then pure False else ioError err
  when exists $ fail ("socket path already exists: " ++ path)

serve :: Options -> Store.Store -> IO ()
serve opts store = do
  let path = socketPath opts
      ready = putStrLn ("Listening on " ++ path)
      install shutdown = mapM_ (\sig -> installHandler sig (Catch shutdown) Nothing) [sigTERM, sigINT]
  assertVacant path
#ifdef COMPARISONS
  if http opts == "snap" then do
    tid <- myThreadId
    let config = Snap.setUnixSocket path . Snap.setAccessLog Snap.ConfigNoLog . Snap.setErrorLog Snap.ConfigNoLog .
          Snap.setVerbose False . Snap.setStartupHook (\_ -> install (throwTo tid UserInterrupt) >> ready) $ Snap.defaultConfig
    (Snap.httpServe config (snapApp (codec opts) store) `catch` \err -> case err of
      UserInterrupt -> pure (); _ -> throwIO (err :: AsyncException))
      `finally` (removeFile path `catch` \err -> unless (isDoesNotExistError err) (ioError err))
  else serveWarp opts store path ready install
#else
  unless (http opts == "warp") $ fail "Snap requires the comparison build: python3 haskell/build.py --comparisons"
  serveWarp opts store path ready install
#endif

serveWarp :: Options -> Store.Store -> FilePath -> IO () -> (IO () -> IO ()) -> IO ()
serveWarp opts store path ready install =
  bracket (N.socket N.AF_UNIX N.Stream N.defaultProtocol) N.close $ \listener -> do
    N.bind listener (N.SockAddrUnix path)
    (do
      N.listen listener 1024
      next <- newIORef 0
      capabilities <- getNumCapabilities
      let forkConnection :: ((forall a. IO a -> IO a) -> IO ()) -> IO ()
          forkConnection action = do
            capability <- atomicModifyIORef' next (\n -> ((n + 1) `mod` capabilities, n))
            _ <- forkOnWithUnmask capability action
            pure ()
      let settings = Warp.setBeforeMainLoop ready . Warp.setInstallShutdownHandler install .
            Warp.setGracefulShutdownTimeout (Just 10) . Warp.setTimeout 10 $ Warp.defaultSettings
      Warp.runSettingsSocket (if pinned opts then Warp.setFork forkConnection settings else settings) listener (app (codec opts) store))
      `finally` removeFile path

main :: IO ()
main = do
  hSetBuffering stdout LineBuffering
  opts <- getArgs >>= parse (Options "../db/db.sqlite" "/tmp/benchmark.sock" Jsonifier "warp" False True True Store.NoCallback)
  if diagnostic opts then Store.inspect (database opts) >>= L.putStr . A.encode
  else Store.withStore (boundWriter opts) (stepMode opts) (database opts) (serve opts)
