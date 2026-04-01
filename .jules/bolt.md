## 2024-06-25 - [Performance] Use os.scandir instead of os.walk
Learning: `os.walk` gathers lists of all items per directory, causing massive overhead on heavy/deep network directories. An iterative stack using `os.scandir` allows us to omit subdirectories dynamically or evaluate `.is_dir()` immediately without separate `stat()` calls.
Action: Whenever a deep directory tree scan exists (`delete_codebase_folders`, `cleanup_nested_dark_mode_folders`), replace `os.walk` with an iterative `os.scandir` stack pattern.
