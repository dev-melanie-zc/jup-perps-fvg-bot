import { useState, useEffect } from 'react'
import RetroMenuBar    from './RetroMenuBar'
import TickerHeader    from './TickerHeader'
import ChartPanel      from './ChartPanel'
import OpenZonesSidebar from './OpenZonesSidebar'
import FvgList         from './FvgList'
import {
  MOCK_CANDLES,
  MOCK_FVGS,
  MOCK_PRICE,
  MOCK_ETH_FVGS,
  MOCK_ETH_PRICE,
} from '../data/mockData'

export default function AppShell() {
  const [ticker, setTicker]   = useState('BTC')
  const [updated, setUpdated] = useState(null)

  const price = ticker === 'BTC' ? MOCK_PRICE : MOCK_ETH_PRICE
  const fvgs  = ticker === 'BTC' ? MOCK_FVGS  : MOCK_ETH_FVGS

  // Simulate periodic "last updated" timestamp
  useEffect(() => {
    const tick = () => {
      const now = new Date()
      setUpdated(now.toLocaleTimeString('en-CA', { hour12: false }))
    }
    tick()
    const id = setInterval(tick, 60000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="flex flex-col h-screen w-screen bg-term-bg overflow-hidden font-mono">
      {/* 1. Old-Windows menu bar */}
      <RetroMenuBar />

      {/* 2. Ticker / price header */}
      <TickerHeader
        activeTicker={ticker}
        onTickerChange={setTicker}
        price={price}
        updatedAt={updated}
      />

      {/* 3. Main dashboard row */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Chart */}
        <ChartPanel
          candles={MOCK_CANDLES}
          fvgs={fvgs}
          ticker={ticker}
        />

        {/* Open Zones sidebar */}
        <OpenZonesSidebar fvgs={fvgs} ticker={ticker} />
      </div>

      {/* 4. FVG list at bottom */}
      <FvgList fvgs={fvgs} ticker={ticker} />
    </div>
  )
}
