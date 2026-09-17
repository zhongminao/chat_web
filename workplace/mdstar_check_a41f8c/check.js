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

items.forEach((src, i) => {
  const html = marked.parse(src, { gfm: true, mangle: false, headerIds: false }).trim();
  const styled = /<(em|strong|code|del)>/.test(html);
  console.log('[' + (i + 1) + '] SRC : ' + JSON.stringify(src));
  console.log('    HTML: ' + html.replace(/\n/g, ' '));
  console.log('    样式: ' + (styled ? '有标签 -> ' + (html.match(/<[^>]+>/g) || []).join(' ') : '纯文本，无标签'));
  console.log('');
});
