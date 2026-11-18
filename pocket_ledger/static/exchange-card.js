(() => {
  const currency = document.querySelector('#quote-currency');
  const amount = document.querySelector('#quote-amount');
  const result = document.querySelector('#quote-result');
  const swap = document.querySelector('#quote-swap');
  const from = document.querySelector('[data-quote-from]');
  const direction = document.querySelector('[data-quote-direction]');
  const quotes = document.querySelectorAll('[data-quote]');
  let reverse = false;

  function scaled(value, places) {
    const [integer, fraction = ''] = value.split('.');
    return BigInt(integer + fraction.padEnd(places, '0'));
  }

  function update() {
    const option = currency.selectedOptions[0];
    const code = option.value;
    const source = reverse ? 'UAH' : code;
    const target = reverse ? code : 'UAH';
    quotes.forEach(quote => { quote.hidden = quote.dataset.quote !== code; });
    from.textContent = source;
    direction.textContent = source + ' to ' + target + (reverse ? ' · Sell rate' : ' · Buy rate');
    const text = amount.value.trim().replace(',', '.');
    const quote = reverse ? option.dataset.sell : option.dataset.buy;
    result.textContent = '—';
    if (!quote || !/^\d+(?:\.\d{0,2})?$/.test(text)) return;

    const precision = 12;
    const rate = scaled(quote, precision);
    const cents = scaled(text, 2);
    const unit = 10n ** BigInt(precision);
    const [numerator, denominator] = reverse ? [cents * unit, rate] : [cents * rate, unit];
    if (!denominator) return;
    const converted = (numerator + denominator / 2n) / denominator;
    result.textContent = (converted / 100n).toLocaleString('en-US') + '.' +
      String(converted % 100n).padStart(2, '0') + ' ' + target;
  }

  currency.addEventListener('change', update);
  amount.addEventListener('input', update);
  swap.addEventListener('click', () => { reverse = !reverse; update(); });
  update();
})();
