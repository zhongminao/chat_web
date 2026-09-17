const { marked } = require('/tmp/node_modules/marked');
const items = [
  '3*4 和 5*6',
  'a*b 与 c*(d)',
  '面积 = 长*宽，对角线 = 2*(长+宽)',
  'P = 2*pi*r，A = pi*r**2',
  '通配路径 packages/chat/src/chat/*.py 与 *.json',
  '设 $E = m*c^2$，且 $x \\in \\mathbb{R}^{*}$',
  '$x^{*}$ 与 $y^{*}',
  '$$ \\begin{align} a &= b \\ c &= d \\end{align}',
  '这是*强调*文字',
  '这是**强调**文字',
  'see *note* here',
  '中文*english*中文',
  '价格 $5 到 $10 之间',
  '`长*宽` 与 `2*(a+b)`',
];
const from = Number(process.argv[2] || 1), to = Number(process.argv[3] || 14);
for (let i = from; i <= to; i++) {
  const html = marked.parse(items[i - 1], { gfm: true }).trim();
  console.log(i + '> ' + html.replace(/\n/g, ' '));
}
