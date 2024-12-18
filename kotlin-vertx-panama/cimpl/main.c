#include <stdlib.h>
#include <assert.h>
#include "sqlite3.h"

int main(int argc, char const *argv[])
{
  sqlite3 *db;
  assert(sqlite3_open_v2("../../db/db.sqlite", &db, SQLITE_OPEN_READWRITE, NULL) == SQLITE_OK);

  assert(sqlite3_close(db) == SQLITE_OK);
  return 0;
}
