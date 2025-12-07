const esbuild = require('esbuild');

const isWatch = process.argv.includes('-w');
const isBuild = !isWatch;
const mode = isBuild ? 'production' : 'development';

const buildOptions = {
    entryPoints: ['wireview/static/wireview/wireview.js'],
    define: {
        'process.env.NODE_ENV': JSON.stringify(mode),
    },
    bundle: true,
    sourcemap: true,
    minify: isBuild,
    target: ['es2020'],
    outfile: 'wireview/static/wireview/wireview.min.js',
};

async function main() {
    if (isWatch) {
        const ctx = await esbuild.context(buildOptions);
        await ctx.watch();
        console.log('Watching for changes...');
    } else {
        await esbuild.build(buildOptions);
        console.log('Build complete.');
    }
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
