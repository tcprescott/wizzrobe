// Only what the reports draw, so the chart bundle stays small; loaded on first chart.
import { BarChart, CustomChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'

echarts.use([BarChart, LineChart, CustomChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, SVGRenderer])

export { echarts }
