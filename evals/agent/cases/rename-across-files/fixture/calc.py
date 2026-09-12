"""价格计算。"""

TAX_RATE = 0.1


def total_price(amount, quantity, taxed=True):
    subtotal = amount * quantity
    return round(subtotal * (1 + TAX_RATE) if taxed else subtotal, 2)
