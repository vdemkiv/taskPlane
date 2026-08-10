import { useState } from "react";

export function App() {
  const [open, setOpen] = useState(false);
  return <Panel open={open} />;
}
