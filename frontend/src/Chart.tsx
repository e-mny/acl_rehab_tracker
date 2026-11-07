import { Area, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Activity } from 'lucide-react';
import { dateLabel, metrics, type Entry, type Metric } from './types';

export default function ProgressChart({ entries, keys, compare = false, height = 280 }: { entries: Entry[]; keys: Metric[]; compare?: boolean; height?: number }) {
  const data = entries.map(e => ({ ...e, timestamp: Date.parse(e.date), ...Object.fromEntries(keys.map(key => [key, compare && ['knee', 'above5', 'above15'].includes(key) ? e[key] != null && e[('other_' + key) as keyof Entry] != null ? +(e[key]! - Number(e[('other_' + key) as keyof Entry])).toFixed(2) : null : e[key]])) }));
  if (!data.some(e => keys.some(k => e[k] != null))) return <div className="chart-empty" style={{ height }}><span className="empty-icon"><Activity size={24} strokeWidth={1.4}/></span><strong>A little data. A clearer picture.</strong><p>{compare ? 'Add measurements for both legs to see the difference.' : 'Your measurements will appear here after your first entry.'}</p></div>;
  return <div className="chart-wrap" role="img" aria-label={`${keys.map(k => metrics[k].label).join(', ')} over time. Exact values available in Journal.`}>
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 18, right: 15, left: -19, bottom: 0 }}>
        <defs><linearGradient id={'fill-' + keys[0]} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={metrics[keys[0]].color} stopOpacity={0.13}/><stop offset="100%" stopColor={metrics[keys[0]].color} stopOpacity={0}/></linearGradient></defs>
        <CartesianGrid vertical={false} stroke="#e8ece8" strokeDasharray="3 5"/>
        <XAxis dataKey="timestamp" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={v => dateLabel(new Date(v).toISOString().slice(0, 10))} tick={{ fontSize: 11, fill: '#7d8680' }} axisLine={false} tickLine={false} minTickGap={35} dy={9}/>
        <YAxis domain={['auto', 'auto']} tick={{ fontSize: 11, fill: '#7d8680' }} axisLine={false} tickLine={false} tickCount={5}/>
        <Tooltip labelFormatter={v => dateLabel(new Date(Number(v)).toISOString().slice(0, 10), true)} formatter={(v, name) => [`${v ?? '—'} ${compare ? 'cm' : metrics[name as Metric]?.unit || ''}`, metrics[name as Metric]?.label || name]} contentStyle={{ border: '1px solid #e4e8e2', borderRadius: 12, fontSize: 12, boxShadow: '0 8px 25px #183d3510' }}/>
        {keys.length === 1 ? <Area type="monotone" dataKey={keys[0]} stroke={metrics[keys[0]].color} strokeWidth={2.5} fill={`url(#fill-${keys[0]})`} dot={{ r: 3, strokeWidth: 2, fill: '#fff' }} activeDot={{ r: 5 }} connectNulls={false} isAnimationActive={false}/> : keys.map(k => <Line key={k} type="monotone" dataKey={k} stroke={metrics[k].color} strokeWidth={2.3} dot={{ r: data.length > 30 ? 0 : 3, fill: '#fff', strokeWidth: 2 }} activeDot={{ r: 5 }} connectNulls={false} isAnimationActive={false}/>)}
        <Legend formatter={name => metrics[name as Metric]?.label || name} iconType="circle" iconSize={7} wrapperStyle={{ fontSize: 11, paddingTop: 25 }}/>
      </ComposedChart>
    </ResponsiveContainer>
  </div>;
}
