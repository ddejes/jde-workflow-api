import React from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import JdeWorkflowApp from "./App.jsx";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <JdeWorkflowApp />
  </React.StrictMode>
);