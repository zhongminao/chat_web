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

const from = parseInt(process.argv[2] || '1', 10);
const to = parseInt(process.argv[3] || String(items.length), 10);

for (let n = from; n <= to; n++) {
  const src = items[n - 1];
  const html = marked.parse(src, { gfm: true }).trim().replace(/\s+/g, ' ');
  const tags = (html.match(/<(em|strong|code|del)>/g) || []).join(',') || '无';
  console.log(n + '|' + html + '|' + tags);
}
