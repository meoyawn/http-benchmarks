{-# LANGUAGE OverloadedStrings #-}
module Codec where

import qualified Data.Aeson as A
import qualified Data.ByteString as B
import qualified Data.ByteString.Lazy as L
import qualified Jsonifier as J
import Model

data Codec = Aeson | Jsonifier deriving (Eq, Show)

decode :: B.ByteString -> Either String NewPost
decode = A.eitherDecodeStrict'

encodeEcho :: Codec -> NewPost -> L.ByteString
encodeEcho Aeson = A.encode
encodeEcho Jsonifier = L.fromStrict . J.toByteString . \r -> J.object
  [("email", J.textString (email r)), ("content", J.textString (content r))]

encodePost :: Codec -> Post -> L.ByteString
encodePost Aeson = A.encode
encodePost Jsonifier = L.fromStrict . J.toByteString . \r -> J.object
  [("id", J.intNumber (fromIntegral (Model.id r))), ("user_id", J.intNumber (fromIntegral (user_id r)))
  ,("content", J.textString (postContent r)), ("created_at", J.intNumber (fromIntegral (created_at r)))
  ,("updated_at", J.intNumber (fromIntegral (updated_at r)))]
