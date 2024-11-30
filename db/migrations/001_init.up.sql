CREATE TABLE users (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  email       TEXT    NOT NULL    COLLATE NOCASE UNIQUE,
  created_at  INTEGER NOT NULL    DEFAULT(unixepoch('subsec') * 1000),
  updated_at  INTEGER NOT NULL    DEFAULT(unixepoch('subsec') * 1000)
);

CREATE TABLE posts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL    REFERENCES users(id),
  content     TEXT    NOT NULL    CHECK(content <> ''),
  created_at  INTEGER NOT NULL    DEFAULT(unixepoch('subsec') * 1000),
  updated_at  INTEGER NOT NULL    DEFAULT(unixepoch('subsec') * 1000)
);
