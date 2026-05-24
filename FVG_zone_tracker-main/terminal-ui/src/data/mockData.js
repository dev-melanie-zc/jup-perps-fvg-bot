// Mock 15m BTC candles — ~6h of data ending near 81400
function makeCandles() {
  const candles = []
  const base = 80800
  let price = base
  // start ~6 hours ago
  let t = Math.floor(Date.now() / 1000) - 6 * 3600
  t = t - (t % 900) // align to 15m boundary

  const seed = [
    0.3, -0.2, 0.5, 0.1, -0.3, 0.4, 0.6, -0.1, 0.2, 0.3,
    -0.4, 0.2, 0.7, -0.2, 0.1, 0.4, -0.3, 0.5, 0.2, -0.1,
    0.6, -0.3, 0.4, 0.1,
  ]

  for (let i = 0; i < 24; i++) {
    const move = seed[i] * 280
    const open = price
    const close = price + move
    const high = Math.max(open, close) + Math.abs(move) * 0.3 + 40
    const low  = Math.min(open, close) - Math.abs(move) * 0.3 - 30
    candles.push({ time: t, open, high, low, close })
    price = close
    t += 900
  }
  return candles
}

export const MOCK_CANDLES = makeCandles()

const lastClose = MOCK_CANDLES[MOCK_CANDLES.length - 1].close

// FVG zones — indexed by candle time
export const MOCK_FVGS = [
  // ── Open bullish FVGs (active) ─────────────────────────────
  {
    id: 'fvg-1',
    type: 'bull',
    zoneType: 'DISCOUNT',
    low: 80610,
    high: 80780,
    mid: 80695,
    timeframe: '15M',
    formedAt: '05-05 06:45 MST',
    filled: false,
    filledAt: null,
  },
  {
    id: 'fvg-2',
    type: 'bull',
    zoneType: 'DISCOUNT',
    low: 80990,
    high: 81140,
    mid: 81065,
    timeframe: '4H',
    formedAt: '05-05 04:00 MST',
    filled: false,
    filledAt: null,
  },
  {
    id: 'fvg-3',
    type: 'bull',
    zoneType: 'DISCOUNT',
    low: 81300,
    high: 81466,
    mid: 81383,
    timeframe: '1H',
    formedAt: '05-05 09:15 MST',
    filled: false,
    filledAt: null,
  },
  // ── Open bearish FVG ──────────────────────────────────────
  {
    id: 'fvg-4',
    type: 'bear',
    zoneType: 'PREMIUM',
    low: 82100,
    high: 82340,
    mid: 82220,
    timeframe: '4H',
    formedAt: '05-04 22:00 MST',
    filled: false,
    filledAt: null,
  },
  // ── Filled zones ──────────────────────────────────────────
  {
    id: 'fvg-5',
    type: 'bull',
    zoneType: 'DISCOUNT',
    low: 79800,
    high: 79970,
    mid: 79885,
    timeframe: '6H',
    formedAt: '05-04 18:00 MST',
    filled: true,
    filledAt: '05-05 02:30 MST',
  },
  {
    id: 'fvg-6',
    type: 'bear',
    zoneType: 'PREMIUM',
    low: 83200,
    high: 83450,
    mid: 83325,
    timeframe: '8H',
    formedAt: '05-04 10:00 MST',
    filled: true,
    filledAt: '05-04 18:00 MST',
  },
  {
    id: 'fvg-7',
    type: 'bull',
    zoneType: 'DISCOUNT',
    low: 80100,
    high: 80260,
    mid: 80180,
    timeframe: '15M',
    formedAt: '05-05 03:30 MST',
    filled: true,
    filledAt: '05-05 06:00 MST',
  },
]

export const MOCK_PRICE = Math.round(lastClose * 10) / 10

export const MOCK_ETH_PRICE = 2361.84

export const MOCK_ETH_FVGS = [
  {
    id: 'e-fvg-1',
    type: 'bull',
    zoneType: 'DISCOUNT',
    low: 2298.5,
    high: 2314.2,
    mid: 2306.35,
    timeframe: '4H',
    formedAt: '05-05 04:00 MST',
    filled: false,
    filledAt: null,
  },
  {
    id: 'e-fvg-2',
    type: 'bear',
    zoneType: 'PREMIUM',
    low: 2410.0,
    high: 2438.5,
    mid: 2424.25,
    timeframe: '6H',
    formedAt: '05-04 18:00 MST',
    filled: false,
    filledAt: null,
  },
  {
    id: 'e-fvg-3',
    type: 'bull',
    zoneType: 'DISCOUNT',
    low: 2240.1,
    high: 2258.9,
    mid: 2249.5,
    timeframe: '8H',
    formedAt: '05-04 10:00 MST',
    filled: true,
    filledAt: '05-05 01:00 MST',
  },
]
