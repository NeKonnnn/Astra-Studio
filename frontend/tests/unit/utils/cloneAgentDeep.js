// Shim: относительные импорты из GPB_ASTRA тестов (jest.requireActual не попадает под jest.mock).
module.exports = jest.requireActual('@/utils/cloneAgentDeep');

