import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth";
import { SiteProvider } from "./site";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <SiteProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </SiteProvider>
    </BrowserRouter>
  </React.StrictMode>
);
