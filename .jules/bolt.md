## 2024-07-06 - Performance optimization on list comprehensions with split()
Learning: Building list comprehensions to map functions after a string split (e.g. `[x.upper() for x in text.split(sep)]`) causes redundant memory allocations for the list. Applying the string method first and then splitting (`text.upper().split(sep)`) skips the list comprehension entirely, being 40-50% faster.
Action: Apply transformations (like `.lower()` or `.upper()`) to strings *before* running `.split()` whenever the logic permits.
