// Dependency-free regression checks for new.html's temporary station-name panel.
// Run: node /home/sim/Trainlog/scripts/test_station_names.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../templates/new.html'), 'utf8');
const start = template.indexOf('          var savedLanguage =');
const end = template.indexOf('        })();', start);
const source = template.slice(start, end)
  .replace('{{ (station_names_setting or langId)|tojson }}', '"ja"')
  .replace('{{ station_script_setting|tojson }}', 'true');
assert.ok(start >= 0 && end > start);
const handlers = {};
const script = { checked: true, addEventListener: (event, fn) => { handlers[event] = fn; } };
const cookies = [];
const displays = [];
let prefilter, combo;
vm.runInNewContext(source, {
  URL,
  location: { href: 'https://trainlog.test/new?stationnames', origin: 'https://trainlog.test' },
  document: {
    set cookie(value) { cookies.push(value); },
    getElementById: () => script,
    querySelector: () => ({ addEventListener: (event, fn) => { handlers.reset = fn; } })
  },
  $: { ajaxPrefilter: fn => { prefilter = fn; } },
  stationScriptFor: value => displays.push(value),
  langCombo: (id, options) => {
    combo = {
      items: ['', 'ru', 'local'].map(value => ({ dataset: { value } })),
      apply: item => options.onApply(item)
    };
    combo.apply(combo.items[0]);
    return combo;
  }
});
function headers(url) {
  const result = {};
  prefilter({ url }, {}, { setRequestHeader: (key, value) => { result[key] = value; } });
  return result;
}
assert.deepEqual(headers('/stationAutocomplete?q=test'), {});
assert.equal(displays.at(-1), 'ja'); // Saved station language, not interface language.
combo.apply(combo.items[1]);
script.checked = false;
handlers.change.call(script);
for (const url of ['/stationAutocomplete?q=test', '/stationTracks']) {
  assert.deepEqual(headers(url), {
    'X-Trainlog-Station-Lang': 'ru', 'X-Trainlog-Station-Script': '0'
  });
}
assert.deepEqual(headers('/unrelated'), {});
assert.deepEqual(headers('https://other.test/stationAutocomplete'), {});
handlers.reset({ preventDefault() {} });
assert.equal(script.checked, true);
assert.equal(displays.at(-1), 'ja');
assert.deepEqual(headers('/stationAutocomplete'), {});
assert.equal(cookies.length, 2);
assert.ok(cookies.every(value => value.endsWith('max-age=0')));
console.log('Station-name panel regression checks passed.');