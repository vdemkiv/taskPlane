import { useState } from 'react';
import { Widget } from './widget';

export function Screen() {
  const [open, setOpen] = useState(false);
  return (
    <main aria-label="shop screen" className="screen">
      <button tabIndex={0} aria-expanded={open}
              onClick={() => setOpen(!open)}>toggle</button>
      <Widget loading={false} error={null} empty={!open} />
    </main>
  );
}
