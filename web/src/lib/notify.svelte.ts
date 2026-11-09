// Whether the Notifications settings window is open (the bell in the sidebar footer, or the
// command palette). Its own module so Nav, App and the palette share it without the store.

export const notifySettings = $state({ open: false });
