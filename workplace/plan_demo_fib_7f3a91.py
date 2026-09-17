#!/usr/bin/env python3
"""plan 工具演示用小脚本：打印前 10 项斐波那契数列及求和。"""


def fib(n: int) -> list[int]:
    seq = [0, 1]
    while len(seq) < n:
        seq.append(seq[-1] + seq[-2])
    return seq[:n]


def main() -> None:
    seq = fib(10)
    print("fib(10) =", seq)
    print("sum     =", sum(seq))
    assert seq == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
    assert sum(seq) == 88
    print("OK: 断言全部通过")


if __name__ == "__main__":
    main()
