"""'After the change' implementation: a deliberate regression -- bubble
sort, which does materially more comparisons than good_impl.py's
insertion sort on the same nearly-sorted input. Real code, really
executed; the regression is detected by actually running this and
comparing against the recorded baseline, not by a canned "regressed"
flag."""


def measure_comparisons(data: list[int]) -> int:
    arr = list(data)
    comparisons = 0
    n = len(arr)
    for i in range(n):
        for j in range(0, n - i - 1):
            comparisons += 1
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return comparisons
