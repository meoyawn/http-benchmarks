{-# LANGUAGE BangPatterns #-}
module Main where
import Control.Exception (evaluate)
import Control.Monad (forM_, foldM)
import Data.Array
import qualified Data.ByteString.Char8 as B
import qualified Data.ByteString.Lazy as L
import GHC.Clock (getMonotonicTimeNSec)
import System.Environment (getArgs)
import Codec

main :: IO ()
main = do
  [path] <- getArgs
  rows <- B.lines <$> B.readFile path
  let count = length rows
      inputs = listArray (0, count - 1) rows
  _ <- evaluate (sum (map B.length rows))
  forM_ [0..5 :: Int] $ \roundNumber ->
    forM_ (if even roundNumber then [Aeson, Jsonifier] else [Jsonifier, Aeson]) $ \codec -> do
      start <- getMonotonicTimeNSec
      checksum <- foldM (\ !acc i -> do
          let body = inputs ! ((i + roundNumber * 17) `mod` count)
          request <- either fail pure (Codec.decode body)
          size <- evaluate (L.length (encodeEcho codec request))
          pure (acc + fromIntegral size)) (0 :: Int) [0 .. count * 4 - 1]
      end <- getMonotonicTimeNSec
      putStrLn (show roundNumber ++ "," ++ show codec ++ "," ++ show (fromIntegral (end-start) / fromIntegral (count*4) :: Double) ++ "," ++ show checksum)
