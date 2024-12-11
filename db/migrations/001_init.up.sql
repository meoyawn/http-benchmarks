CREATE TABLE users (
  id          INTEGER NOT NULL  PRIMARY KEY AUTOINCREMENT,
  email       TEXT    NOT NULL  COLLATE NOCASE UNIQUE,
  created_at  INTEGER NOT NULL  DEFAULT(unixepoch('subsec') * 1000),
  updated_at  INTEGER NOT NULL  DEFAULT(unixepoch('subsec') * 1000)
) STRICT;

CREATE TABLE posts (
  id          INTEGER NOT NULL  PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL  REFERENCES users(id),
  content     TEXT    NOT NULL  CHECK(content <> ''),
  created_at  INTEGER NOT NULL  DEFAULT(unixepoch('subsec') * 1000),
  updated_at  INTEGER NOT NULL  DEFAULT(unixepoch('subsec') * 1000)
) STRICT;
