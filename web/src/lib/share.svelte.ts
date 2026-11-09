// What the share sheet handed the app, waiting for the quick-add bar to pick it up (the bar
// may mount after the address was read, on sign-in or when the scope page appears).
// `line` is taken once into the input; `body` rides along as the item's description until the
// item is filed or the chip is dropped.
export const share = $state<{ line: string | null; body: string | null }>({ line: null, body: null });
