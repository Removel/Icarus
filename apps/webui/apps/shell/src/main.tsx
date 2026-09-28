import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import '@icarus/ui/styles.css';
import './workspace.css';
import './design.css';

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
