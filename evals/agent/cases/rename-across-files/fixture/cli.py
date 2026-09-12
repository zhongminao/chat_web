"""命令行入口。"""
import argparse

from calc import total_price


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("amount", type=float)
    parser.add_argument("quantity", type=float)
    args = parser.parse_args()
    print(total_price(args.amount, args.quantity))


if __name__ == "__main__":
    main()
