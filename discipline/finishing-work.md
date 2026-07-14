# Finishing work (after EM sign-off)

Done isn't merged; merged isn't shipped. After the human signs off:

1. Run the retro (`tp.py loop retro`) — lessons land in the KB while the
   context is fresh; forecast accuracy feeds the refinement thesis.
2. Resolve tracked debt or schedule it: every quick-mode task left a
   D-item; either fix it now or make it a requirement with an owner.
3. Update the graph (`tp.py graph scan`) so the next change's blast radius
   is computed against reality.
4. Close the track (`tp.py track close <name>`), branch per your
   workflow.md conventions (merge/PR — never push from a governed agent;
   `git push` is deny-listed by default and that's deliberate).
5. If the shape of the system changed, `knowledge/architecture.md` must
   say so — the architecture lens goes blind otherwise.
