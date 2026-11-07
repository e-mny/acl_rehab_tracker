import { defineConfig } from '@playwright/test';
export default defineConfig({ testDir: './tests', timeout: 60000, workers: 1, use: { baseURL: process.env.STRIDE_TEST_URL || 'http://127.0.0.1:18080', viewport: { width: 1440, height: 1000 }, screenshot: 'only-on-failure' } });
