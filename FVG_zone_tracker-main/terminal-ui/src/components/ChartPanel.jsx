import { useEffect, useRef } from 'react'
import { createChart, LineStyle } from 'lightweight-charts'

export default function ChartPanel({ candles, fvgs, ticker }) {
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const seriesRef    = useRef(null)

  // Init chart once
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background:  { color: '#0a0e14' },
        textColor:   '#5a6a7e',
        fontFamily:  '"Courier New", Courier, monospace',
        fontSize:    10,
      },
      grid: {
        vertLines: { color: '#131b26', style: LineStyle.Solid },
        horzLines: { color: '#131b26', style: LineStyle.Solid },
      },
      crosshair: {
        vertLine: { color: '#39d353', labelBackgroundColor: '#0d1219', width: 1, style: LineStyle.Dashed },
        horzLine: { color: '#39d353', labelBackgroundColor: '#0d1219', width: 1, style: LineStyle.Dashed },
      },
      rightPriceScale: {
        borderColor:  '#1e2a38',
        textColor:    '#5a6a7e',
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor:     '#1e2a38',
        textColor:       '#5a6a7e',
        timeVisible:     true,
        secondsVisible:  false,
        fixLeftEdge:     true,
      },
      handleScroll:  true,
      handleScale:   true,
    })

    const series = chart.addCandlestickSeries({
      upColor:          '#39d353',
      downColor:        '#e05260',
      borderUpColor:    '#39d353',
      borderDownColor:  '#e05260',
      wickUpColor:      '#39d353',
      wickDownColor:    '#e05260',
    })

    chartRef.current  = chart
    seriesRef.current = series

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({
          width:  containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        })
      }
    })
    ro.observe(containerRef.current)
    chart.applyOptions({
      width:  containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    })

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current  = null
      seriesRef.current = null
    }
  }, [])

  // Update candle data
  useEffect(() => {
    if (!seriesRef.current || !candles?.length) return
    seriesRef.current.setData(candles)
    chartRef.current?.timeScale().fitContent()
  }, [candles])

  // Draw FVG price lines when fvgs change
  useEffect(() => {
    if (!seriesRef.current || !fvgs) return

    // Remove old price lines by recreating the series isn't ideal;
    // instead we use PriceLine api: clear all then re-add
    // lightweight-charts doesn't have a "removeAllPriceLines" — track refs
    const lines = []
    fvgs.forEach(z => {
      if (z.filled) return
      const isBull = z.type === 'bull'
      const color  = isBull ? '#39d353' : '#e05260'
      const dimColor = isBull ? 'rgba(57,211,83,0.35)' : 'rgba(224,82,96,0.35)'

      lines.push(seriesRef.current.createPriceLine({
        price:      z.high,
        color:      color,
        lineWidth:  1,
        lineStyle:  LineStyle.Dashed,
        axisLabelVisible: true,
        title:      isBull ? `▲ ${z.timeframe}` : `▼ ${z.timeframe}`,
      }))
      lines.push(seriesRef.current.createPriceLine({
        price:     z.low,
        color:     dimColor,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
      }))
    })

    return () => {
      lines.forEach(l => {
        try { seriesRef.current?.removePriceLine(l) } catch (_) {}
      })
    }
  }, [fvgs])

  return (
    <div className="flex flex-col flex-1 min-h-0 bg-term-bg border-r border-term-border">
      {/* Chart sub-header */}
      <div className="flex items-center gap-3 px-3 py-1 border-b border-term-border bg-term-panel flex-shrink-0">
        <span className="text-3xs text-term-muted tracking-wider">CHART</span>
        <span className="text-3xs text-term-green">●</span>
        <span className="text-3xs text-term-muted">{ticker}USDT · 15M · Phemex</span>
        <div className="ml-auto flex items-center gap-2 text-3xs text-term-muted">
          <span className="px-1 border border-term-border text-term-green opacity-60">FVG</span>
          <span className="px-1 border border-term-border opacity-40">EMA</span>
          <span className="px-1 border border-term-border opacity-40">VOL</span>
        </div>
      </div>

      {/* Lightweight-charts mount point */}
      <div ref={containerRef} className="flex-1 min-h-0" />
    </div>
  )
}
