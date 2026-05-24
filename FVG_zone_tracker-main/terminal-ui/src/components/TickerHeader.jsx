export default function TickerHeader({ activeTicker, onTickerChange, price, updatedAt }) {
  const tickers = ['BTC', 'ETH']

  const now = new Date()
  const timeStr = now.toLocaleTimeString('en-CA', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })

  return (
    <div className="flex items-center gap-0 h-8 bg-term-menubg border-b border-term-border flex-shrink-0 px-3 select-none">

      {/* Ticker selector */}
      <div className="flex items-center gap-px mr-4">
        {tickers.map(t => (
          <button
            key={t}
            onClick={() => onTickerChange(t)}
            className={[
              'px-3 h-8 text-2xs font-bold tracking-wider border-b-2 transition-colors cursor-default',
              activeTicker === t
                ? 'text-term-blue border-term-blue bg-[rgba(74,144,217,0.10)]'
                : 'text-term-muted border-transparent hover:text-term-text hover:border-term-border',
            ].join(' ')}
          >
            {t}USDT
          </button>
        ))}
      </div>

      {/* Divider */}
      <div className="w-px h-4 bg-term-border mr-4" />

      {/* Price */}
      <div className="flex items-baseline gap-2">
        <span className="text-term-green font-bold text-base tracking-wider leading-none">
          {typeof price === 'number'
            ? price.toLocaleString('en-US', { minimumFractionDigits: activeTicker === 'ETH' ? 2 : 1, maximumFractionDigits: activeTicker === 'ETH' ? 2 : 1 })
            : '—'}
        </span>
        <span className="text-3xs text-term-muted">USDT</span>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Right side: timezone + time */}
      <div className="flex items-center gap-4 text-3xs text-term-muted">
        <span>MST/MDT · Alberta</span>
        <span className="text-term-green opacity-50">{timeStr}</span>
        {updatedAt && (
          <span className="opacity-40">upd {updatedAt}</span>
        )}
      </div>
    </div>
  )
}
