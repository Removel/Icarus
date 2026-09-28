import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import '@icarus/ui/styles.css';
import './design.css';
import './workspace.css';
import './consumer.css';
import './navigation.css';
import './canvas.css';

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
