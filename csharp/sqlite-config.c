#include "sqlite3.h"
int benchmark_config_int(int option, int value) {
    return sqlite3_config(option, value);
}
int benchmark_config_out(int option, int *value) {
    return sqlite3_config(option, value);
}
int benchmark_config_pagecache(int option, void *buffer, int size, int count) {
    return sqlite3_config(option, buffer, size, count);
}
