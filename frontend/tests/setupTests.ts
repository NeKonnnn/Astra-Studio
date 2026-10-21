import '@testing-library/jest-dom';
import { TextEncoder, TextDecoder } from 'util';

// react-router-dom v7 expects TextEncoder in the Jest/jsdom environment.
if (typeof global.TextEncoder === 'undefined') {
  (global as unknown as { TextEncoder: typeof TextEncoder }).TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
  (global as unknown as { TextDecoder: typeof TextDecoder }).TextDecoder = TextDecoder as typeof global.TextDecoder;
}
