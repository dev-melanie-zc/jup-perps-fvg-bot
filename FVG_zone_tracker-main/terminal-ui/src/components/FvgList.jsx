function fmtRange(z, ticker) {
  const dec = ticker === 'ETH' ? 2 : 1
  return `${z.low.toFixed(dec)} – ${z.high.toFixed(dec)}`
}

function ZoneRow({ z, ticker }) {
  const isBull   = z.type === 'bull'
  const isOpen   = !z.filled
  const arrow    = isBull ? '▲' : '▼'
  const label    = isBull ? 'Bullish FVG' : 'Bearish FVG'
  const zoneType = isBull ? 'DISCOUNT' : 'PREMIUM'
  const action   = isOpen ? (isBull ? 'LONG setup' : 'SHORT setup') : 'FILLED'

  return (
    <div
      className={[
        'flex items-center gap-0 px-3 py-1 border-b text-3xs font-mono',
        'border-term-border transition-colors',
        isOpen && isBull  ? 'bg-[rgba(57,211,83,0.04)] hover:bg-[rgba(57,211,83,0.08)]' : '',
        isOpen && !isBull ? 'bg-[rgba(224,82,96,0.04)] hover:bg-[rgba(224,82,96,0.08)]' : '',
        !isOpen           ? 'opacity-35' : '',
      ].join(' ')}
    >
      {/* TF badge */}
      <span className="w-8 text-term-muted opacity-60 flex-shrink-0">[{z.timeframe}]</span>

      {/* Arrow + label */}
      <span className={[
        'flex-shrink-0 mr-1 font-bold',
        isBull ? 'text-term-green' : 'text-term-red',
        !isOpen ? 'opacity-50' : '',
      ].join(' ')}>
        {arrow}
      </span>

      <span className={[
        'flex-1 tracking-wide',
        !isOpen ? 'text-term-muted' : 'text-term-text',
      ].join(' ')}>
        {label} ({zoneType})&nbsp;&nbsp;
        <span className={isBull ? 'text-term-green' : 'text-term-red'}>
          {fmtRange(z, ticker)}
        </span>
        &nbsp;·&nbsp;
        <span className="text-term-muted opacity-60">
          formed {z.formedAt}
        </span>
        {z.filled && z.filledAt && (
          <span className="text-term-muted opacity-40">
            &nbsp;· filled {z.filledAt}
          </span>
        )}
      </span>

      {/* Action badge */}
      <span className={[
        'flex-shrink-0 ml-2 px-2 py-px border text-3xs font-bold tracking-wider',
        isOpen && isBull  ? 'border-term-green text-term-green bg-[rgba(57,211,83,0.10)]' : '',
        isOpen && !isBull ? 'border-term-red text-term-red bg-[rgba(224,82,96,0.10)]' : '',
        !isOpen           ? 'border-term-border text-term-muted' : '',
      ].join(' ')}
        style={{ borderRadius: 1 }}
      >
        {action}
      </span>
    </div>
  )
}

export default function FvgList({ fvgs, ticker }) {
  const sorted = [...(fvgs || [])].sort((a, b) => {
    // Open first, then by timeframe, then filled
    if (a.filled !== b.filled) return a.filled ? 1 : -1
    return 0
  })

  const openCount   = sorted.filter(z => !z.filled).length
  const filledCount = sorted.filter(z =>  z.filled).length

  return (
    <div className="flex flex-col border-t border-term-border bg-term-panel flex-shrink-0" style={{ maxHeight: '34vh' }}>

      {/* List header */}
      <div className="flex items-center gap-3 px-3 py-1 border-b border-term-border flex-shrink-0">
        <span className="text-3xs text-term-muted tracking-widest">FVG LOG</span>
        <span className="text-3xs text-term-muted">──</span>
        <span className="text-3xs text-term-green">{openCount} open</span>
        <span className="text-3xs text-term-muted opacity-40">{filledCount} filled</span>
        <div className="ml-auto text-3xs text-term-muted opacity-40">
          [TF] ▲/▼ Type (Zone) · Range · Timestamp
        </div>
      </div>

      {/* Rows */}
      <div className="overflow-y-auto flex-1">
        {!sorted.length && (
          <p className="text-3xs text-term-muted px-3 py-2">no zones detected</p>
        )}
        {sorted.map(z => (
          <ZoneRow key={z.id} z={z} ticker={ticker} />
        ))}
      </div>
    </div>
  )
}
