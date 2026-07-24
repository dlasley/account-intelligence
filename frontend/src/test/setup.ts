// Global Vitest/jsdom polyfills. jsdom doesn't implement ResizeObserver, which
// Radix primitives (ScrollArea, and anything Popper-positioned) construct on
// mount — without this, any test that renders one of those crashes with
// "ResizeObserver is not defined" regardless of what the test itself asserts.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
}
