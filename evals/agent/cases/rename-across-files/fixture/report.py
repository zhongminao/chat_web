"""打印一张账单。"""
from calc import total_price


def render(rows):
    lines = []
    for name, amount, quantity in rows:
        lines.append(f"{name}\t{total_price(amount, quantity)}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render([("apple", 3.5, 4), ("pear", 2.0, 3)]))
