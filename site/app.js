'use strict';
const $ = id => document.getElementById(id);
const repo = 'neutro74/umc-registry';
let catalog = [], prepared = null;
function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function render() {
  const search = $('search').value.toLowerCase();
  const matches = catalog.filter(p => [p.id, p.name, p.description, p.owner, ...(p.permissions || [])].join(' ').toLowerCase().includes(search));
  $('plugins').replaceChildren(); $('count').textContent = String(matches.length);
  $('catalog-status').textContent = matches.length ? '' : catalog.length ? 'No matching plugins.' : 'No plugins published yet.';
  for (const p of matches) {
    const card = element('article', undefined, 'plugin-row');
    const info = element('div', undefined, 'plugin-info');
    info.append(element('h3', p.name));
    const version = element('p', undefined, 'plugin-version');
    version.append(element('span', `${p.id}@${p.version}`));
    const source = element('a', 'Source'); source.href = p.source; source.target = '_blank'; source.rel = 'noopener'; version.append(source);
    info.append(version);
    const defaults = {
      'coords':'Read your current coordinates.',
      'coords-hud':'Show your coordinates on the HUD.',
      'local-commands':'Use .coords in chat to show your position.',
      'tick-counter':'Count game ticks.'
    };
    const description = p.description || defaults[p.id];
    if (description) info.append(element('p', description, 'plugin-description'));
    const author = element('div', undefined, 'plugin-author');
    const profile = element('a', p.owner); profile.href = `https://github.com/${encodeURIComponent(p.owner)}`; profile.target = '_blank'; profile.rel = 'noopener'; author.append(profile);
    const access = element('div', p.permissions.length ? p.permissions.join(', ') : 'none', 'plugin-access');
    access.setAttribute('aria-label', `Permissions: ${p.permissions.length ? p.permissions.join(', ') : 'none'}`);
    card.append(info, author, access);
    const command = `umc install ${p.id}@${p.version}`, install = element('div', undefined, 'install');
    const copy = element('button', 'Copy'); copy.type = 'button'; copy.setAttribute('aria-label', `Copy install command for ${p.name}`);
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(command); copy.textContent = 'Copied'; }
      catch { copy.textContent = 'Select text'; }
    });
    install.append(element('code', command), copy); card.append(install);
    $('plugins').append(card);
  }
}
$('search').addEventListener('input', render);
fetch('catalog.json').then(r => { if (!r.ok) throw Error('Catalog unavailable'); return r.json(); }).then(data => {
  if (!Array.isArray(data)) throw Error('Invalid catalog');
  catalog = data.sort((a,b) => a.name.localeCompare(b.name) || b.version.localeCompare(a.version)); render();
}).catch(() => { $('catalog-status').textContent = 'The catalog could not be loaded. Refresh the page or browse the registry on GitHub.'; });
// Parse declaration strings for preview only. The native compiler validates submissions in CI.
function metadata(source) {
  const tokens = []; let i = 0;
  while (i < source.length) {
    const c = source[i];
    if (/\s/.test(c)) { i++; continue; }
    if (source.startsWith('//', i)) { const end = source.indexOf('\n', i); i = end < 0 ? source.length : end + 1; continue; }
    if (c === '"') {
      i++; let text = '', closed = false;
      while (i < source.length) {
        let ch = source[i++];
        if (ch === '"') { closed = true; break; }
        if (ch === '\\') {
          const escapes = {n:'\n',r:'\r',t:'\t','0':'\0','\\':'\\','"':'"'};
          ch = source[i++]; if (!(ch in escapes)) throw Error('Unsupported string escape.'); ch = escapes[ch];
        }
        text += ch;
      }
      if (!closed) throw Error('Unterminated string.');
      tokens.push({string:text}); continue;
    }
    const identifier = source.slice(i).match(/^[A-Za-z_][A-Za-z_0-9]*/);
    if (identifier) { tokens.push(identifier[0]); i += identifier[0].length; }
    else { tokens.push(c); i++; }
  }
  if (tokens[0] !== 'plugin' || !tokens[1] || typeof tokens[1].string !== 'string' || tokens[2] !== ';') throw Error('Start the file with plugin "id";');
  const result = {id:tokens[1].string, version:'1.0.0'}; let depth = 0, start = true;
  for (let n = 3; n < tokens.length; n++) {
    if (tokens[n] === '{') { depth++; start = false; }
    else if (tokens[n] === '}') { depth--; if (!depth) start = true; }
    else if (!depth && start && tokens[n] === 'version') {
      if (typeof tokens[n+1]?.string !== 'string' || tokens[n+2] !== ';') throw Error('Invalid version declaration.');
      result.version = tokens[n+1].string; n += 2; start = true;
    } else if (!depth) start = tokens[n] === ';';
  }
  if (!/^[a-z0-9]+(?:-+[a-z0-9]+)*$/.test(result.id) || result.id.length > 64) throw Error('Use a lowercase plugin ID with letters, digits and hyphens.');
  if (!/^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$/.test(result.version) || result.version.includes('..')) throw Error('Invalid plugin version.');
  return result;
}
$('submit-form').addEventListener('submit', async event => {
  event.preventDefault(); $('prepared').hidden = true; prepared = null;
  $('form-status').className = ''; $('form-status').textContent = 'Reading file…';
  try {
    const file = $('file').files[0];
    if (!file || !file.name.endsWith('.umc') || file.size > 262144) throw Error('Choose a .umc file no larger than 256 KB.');
    const url = new URL($('source').value.trim());
    if (url.protocol !== 'https:' || !['github.com','raw.githubusercontent.com'].includes(url.host) || url.search || url.hash || !url.pathname.endsWith('.umc')) throw Error('Use a public GitHub release-asset or raw .umc URL.');
    if (url.host === 'github.com' && !/^\/[A-Za-z0-9-]+\/[^/]+\/releases\/download\/[^/]+\/[^/]+\.umc$/.test(url.pathname)) throw Error('Use the release asset URL, not the GitHub file view.');
    const bytes = await file.arrayBuffer(), meta = metadata(new TextDecoder('utf-8', {fatal:true}).decode(bytes));
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const sha256 = Array.from(new Uint8Array(digest), x => x.toString(16).padStart(2,'0')).join('');
    prepared = {...meta, sha256, source:url.href};
    const body = JSON.stringify(prepared, null, 2) + '\n'; $('manifest').value = body;
    $('preview').replaceChildren();
    for (const [key,value] of [['Plugin',meta.id],['Version',meta.version],['SHA-256',sha256]]) $('preview').append(element('dt',key),element('dd',value));
    const query = new URLSearchParams({filename:`plugins/${meta.id}/${meta.version}.json`, value:body});
    $('github-submit').href = `https://github.com/${repo}/new/main?${query}`;
    $('prepared').hidden = false; $('form-status').textContent = 'Ready to submit. The checks will verify the download matches this file.';
  } catch (error) { $('form-status').className = 'error'; $('form-status').textContent = error.message; }
});
$('download-manifest').addEventListener('click', () => {
  if (!prepared) return;
  const url = URL.createObjectURL(new Blob([$('manifest').value], {type:'application/json'}));
  const link = element('a'); link.href = url; link.download = `${prepared.id}-${prepared.version}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
