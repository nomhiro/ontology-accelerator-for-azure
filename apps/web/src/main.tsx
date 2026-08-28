import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

const container = document.getElementById("root");
if (!container) {
  throw new Error("#root が見つかりません");
}

createRoot(container).render(
  <StrictMode>
    <FluentProvider theme={webLightTheme}>
      <App />
    </FluentProvider>
  </StrictMode>,
);
