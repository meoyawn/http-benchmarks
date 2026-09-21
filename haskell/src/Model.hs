{-# LANGUAGE TemplateHaskell #-}
module Model where

import Data.Aeson.TH
import Data.Int (Int64)
import Data.Text (Text)
import qualified Data.Text as T

data NewPost = NewPost { email :: !Text, content :: !Text } deriving (Eq, Show)
$(deriveJSON defaultOptions ''NewPost)

data Post = Post
  { id :: !Int64, user_id :: !Int64, postContent :: !Text
  , created_at :: !Int64, updated_at :: !Int64
  } deriving (Eq, Show)
$(deriveJSON defaultOptions { fieldLabelModifier = \s -> if s == "postContent" then "content" else s } ''Post)

-- Exact ASCII equivalent of the shared whole-string regular expression.
validEmail :: Text -> Bool
validEmail value = case T.splitOn (T.singleton '@') value of
  [local, domain] ->
    not (T.null local) && T.all localChar local &&
    let (prefix, suffix) = T.breakOnEnd (T.singleton '.') domain
    in T.length prefix > 1 && T.all domainChar (T.dropEnd 1 prefix) &&
       T.length suffix >= 2 && T.all asciiAlpha suffix
  _ -> False
  where
    asciiAlpha c = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
    digit c = c >= '0' && c <= '9'
    domainChar c = asciiAlpha c || digit c || c == '.' || c == '-'
    localChar c = asciiAlpha c || digit c || c `elem` ("._%+-" :: String)

validate :: NewPost -> [Text]
validate request =
  [T.pack "email: invalid address" | not (validEmail (email request))] ++
  [T.pack "content: must be at least 1 character" | T.null (content request)]
