const TF_ORDER = ['15M', '1H', '4H', '6H', '8H', '12H']

function fmtRange(z, ticker) {
  const dec = ticker === 'ETH' ? 2 : 1
  return `${z.low.toFixed(dec)} – ${z.high.toFixed(dec)}`
}

export default function OpenZonesSidebar({ fvgs, ticker }) {
  const open = (fvgs || []).filter(z => !z.filled)

  // Group by timeframe in canonical order
  const grouped = {}
  TF_ORDER.forEach(tf => {
    const zones = open.filter(z => z.timeframe === tf)
    if (zones.length) grouped[tf] = zones
  })
  // Catch any TFs not in TF_ORDER
  open.forEach(z => {
    if (!TF_ORDER.includes(z.timeframe)) {
      grouped[z.timeframe] = grouped[z.timeframe] || []
      if (!grouped[z.timeframe].includes(z)) grouped[z.timeframe].push(z)
    }
  })

  return (
    <div className="w-48 flex-shrink-0 flex flex-col bg-term-panel border-l border-term-border overflow-hidden">

      {/* Sidebar header */}
      <div className="px-2 py-1 border-b border-term-border flex-shrink-0">
        <div className="text-3xs text-term-muted tracking-widest">OPEN ZONES</div>
        <div className="text-3xs text-term-green opacity-50">{open.length} active</div>
      </div>

      {/* Zone cards */}
      <div className="flex-1 overflow-y-auto px-1 py-1 space-y-2">
        {!open.length && (
          <p className="text-3xs text-term-muted px-1 pt-2">no open zones</p>
        )}

        {Object.entries(grouped).map(([tf, zones]) => (
          <div key={tf}>
            {/* TF label */}
            <div className="text-3xs text-term-muted tracking-widest px-1 mb-1 opacity-60">
              [{tf}]
            </div>

            {zones.map(z => {
              const isBull = z.type === 'bull'
              return (
                <div
                  key={z.id}
                  className={[
                    'px-2 py-1 mb-1 border text-3xs font-mono',
                    isBull
                      ? 'border-term-green bg-term-greenbg text-term-green'
                      : 'border-term-red bg-term-redbg text-term-red',
                  ].join(' ')}
                  style={{ borderRadius: 2 }}
                >
                  <div className="font-bold tracking-wider">
                    {isBull ? '▲ DISCOUNT' : '▼ PREMIUM'}
                  </div>
                  <div className="mt-px opacity-90">
                    {fmtRange(z, ticker)}
                  </div>
                  <div className="mt-px opacity-50">
                    {z.formedAt}
                  </div>
                </div>
              )
            })}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="px-2 py-1 border-t border-term-border flex-shrink-0">
        <span className="text-3xs text-term-muted opacity-40">
          {(fvgs || []).filter(z => z.filled).length} filled
        </span>
      </div>
    </div>
  )
}
