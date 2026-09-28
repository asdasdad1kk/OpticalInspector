export {};

declare global {
  interface Window {
    pywebview?: {
      api?: {
        minimize(): void;
        toggleMaximize(): void;
        close(): void;
      };
    };
  }
}