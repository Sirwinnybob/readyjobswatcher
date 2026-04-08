## 2024-06-25 - [Performance] Use os.scandir instead of os.walk
Learning: `os.walk` gathers lists of all items per directory, causing massive overhead on heavy/deep network directories. An iterative stack using `os.scandir` allows us to omit subdirectories dynamically or evaluate `.is_dir()` immediately without separate `stat()` calls.
Action: Whenever a deep directory tree scan exists (`delete_codebase_folders`, `cleanup_nested_dark_mode_folders`), replace `os.walk` with an iterative `os.scandir` stack pattern.
## 2026-04-01 - [Avoid O(N) string prefix checks in frequent file paths] **Learning:** File processing loops that check for prefixes can be optimized significantly by pre-computing the prefixes into a tuple, allowing `startswith()` to execute in fast C code rather than a slow Python `for` loop. **Action:** Next time I see a loop doing `if val.startswith(prefix) for prefix in collection`, replace it with `val.startswith(tuple_of_prefixes)`.

## 2024-04-01 - PyMuPDF get_pixmap with clip Optimization
Learning: Rendering a full PDF page into a pixmap and then using Pillow (`PIL`) to crop the image `Image.crop()` is highly inefficient, taking 10x longer and consuming much more memory.
Action: Use PyMuPDF's built-in clipping feature `page.get_pixmap(clip=rect)` to natively render only the exact sub-region required. This pushes the logic down to the optimized C implementation, yielding over a 10x speedup for sub-image analysis.
## 2024-05-24 - Unnecessary List Allocation inside all()
Learning: Using a list comprehension inside `all()` or `any()` (e.g., `all([x in y for x in z])`) forces Python to evaluate the entire iterable and allocate a list in memory before checking any conditions. This defeats the short-circuiting nature of `all()`.
Action: Always use generator expressions (e.g., `all(x in y for x in z)`) for such checks. This avoids memory allocation and allows `all()` to return `False` immediately upon encountering the first falsy value, providing significant speedups (over 80% in our benchmarks) and reducing memory overhead.
