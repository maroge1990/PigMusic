// Loaded before the unit tests (npm test): the client's modules import each
// other without the .ts extension, as Vite resolves them; Node needs it.
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context);
    } catch (error) {
      if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier))
        return next(specifier + ".ts", context);
      throw error;
    }
  },
});
