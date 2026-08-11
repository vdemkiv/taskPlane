import useSWR from "swr";
// calls the pricing service over HTTP — NOT an import edge
export function CartPanel(){ const {data}=useSWR("/api/pricing/quote"); return <div>{data}</div>; }
