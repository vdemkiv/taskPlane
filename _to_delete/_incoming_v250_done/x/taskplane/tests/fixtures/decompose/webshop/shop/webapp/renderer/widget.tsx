import { useState } from 'react';

export function Widget(props: {loading: boolean, error: any, empty: boolean}) {
  const [count, setCount] = useState(0);
  if (props.loading) return <span role="status">loading…</span>;
  if (props.error) return <span role="alert">failed</span>;
  if (props.empty) return <span className="empty">nothing yet</span>;
  return <button aria-label="add" onClick={() => setCount(count + 1)}>
    {count}</button>;
}
