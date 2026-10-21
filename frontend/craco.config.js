/**
 * CRACO: точечные правки webpack CRA без eject.
 *
 * LOW_MEMORY_BUILD=true — режим для Docker/CI с жёстким лимитом RAM:
 * - без ForkTsChecker (отдельный процесс tsc не дублирует память);
 * - minify без параллельных worker'ов.
 *
 * Локально обычный `npm run build` без флага — быстрее.
 * Проверка типов при low-memory: `npm run typecheck` (отдельно).
 */
const lowMemoryBuild = process.env.LOW_MEMORY_BUILD === 'true';

function isPlugin(plugin, name) {
  return Boolean(plugin && plugin.constructor && plugin.constructor.name === name);
}

module.exports = {
  webpack: {
    configure: (webpackConfig) => {
      if (!lowMemoryBuild) {
        return webpackConfig;
      }

      webpackConfig.plugins = (webpackConfig.plugins || []).filter(
        (plugin) => !isPlugin(plugin, 'ForkTsCheckerWebpackPlugin'),
      );

      const minimizers = webpackConfig.optimization && webpackConfig.optimization.minimizer;
      if (Array.isArray(minimizers)) {
        for (const minimizer of minimizers) {
          if (isPlugin(minimizer, 'TerserPlugin') && minimizer.options) {
            minimizer.options.parallel = false;
          }
        }
      }

      return webpackConfig;
    },
  },
  jest: {
    configure: (jestConfig) => {
      // Тесты живут в frontend/tests/ (как backend/tests/), не рядом с src.
      jestConfig.roots = ['<rootDir>/src', '<rootDir>/tests'];
      jestConfig.testMatch = [
        '<rootDir>/tests/**/*.{spec,test}.{js,jsx,ts,tsx}',
      ];
      jestConfig.setupFilesAfterEnv = [
        ...((jestConfig.setupFilesAfterEnv || []).filter(
          (p) => !String(p).includes('setupTests'),
        )),
        '<rootDir>/tests/setupTests.ts',
      ];
      jestConfig.moduleNameMapper = {
        ...(jestConfig.moduleNameMapper || {}),
        '^@/(.*)$': '<rootDir>/src/$1',
        // react-router-dom v7 exports map poorly under Jest 27 / CRA
        '^react-router-dom$': '<rootDir>/node_modules/react-router-dom/dist/index.js',
        '^react-router$': '<rootDir>/node_modules/react-router/dist/development/index.js',
        '^react-router/dom$': '<rootDir>/node_modules/react-router/dist/development/dom-export.js',
      };
      return jestConfig;
    },
  },
};
