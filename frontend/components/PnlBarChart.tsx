"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export type PnlBarDatum = {
  name: string;
  value: number;
  fill: string;
};

export default function PnlBarChart({ data }: { data: PnlBarDatum[] }) {
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#404040" strokeOpacity={0.3} />
          <XAxis dataKey="name" />
          <YAxis tickFormatter={(v: number) => `$${v}`} />
          <Tooltip formatter={(v) => `$${Number(v).toFixed(2)}`} />
          <Bar dataKey="value">
            {data.map((d) => (
              <Cell key={d.name} fill={d.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
