// pnpm hook: the root apps/web/package.json pins @types/hls.js@^1.4.0 which
// does not exist on the registry (only 1.0.0 is published). Remap to a
// resolvable version so the tree installs. This file is NOT package.json.
module.exports = {
  hooks: {
    readPackage(pkg) {
      if (pkg.dependencies && pkg.dependencies["@types/hls.js"]) {
        pkg.dependencies["@types/hls.js"] = "1.0.0";
      }
      if (pkg.devDependencies && pkg.devDependencies["@types/hls.js"]) {
        pkg.devDependencies["@types/hls.js"] = "1.0.0";
      }
      // vitest-ui is not a real package (likely meant @vitest/ui). Drop it.
      if (pkg.devDependencies && pkg.devDependencies["vitest-ui"]) {
        delete pkg.devDependencies["vitest-ui"];
      }
      return pkg;
    },
  },
};
