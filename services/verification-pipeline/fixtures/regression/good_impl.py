"""'Before the change' implementation: insertion sort, efficient on the
nearly-sorted fixture input. Real code, really executed -- the reported
metric (comparison count) is a genuine measurement of this function's
real behavior, not a hardcoded number."""


def measure_comparisons(data: list[int]) -> int:
    arr = list(data)
    comparisons = 0
    for i in range(1, len(arr)):
        key = arr[i]
        j = i - 1
        while j >= 0:
            comparisons += 1
            if arr[j] <= key:
                break
            arr[j + 1] = arr[j]
            j -= 1
        arr[j + 1] = key
    return comparisons
