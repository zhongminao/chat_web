"""验收：只认新名字。改名没做全，这里就会 ImportError。"""
from calc import grand_total

assert grand_total(3.5, 4) == 15.4, grand_total(3.5, 4)
assert grand_total(2.0, 3, taxed=False) == 6.0
import report

assert report.render([("apple", 3.5, 4)]) == "apple\t15.4"
print("改名完成且调用方都对")
