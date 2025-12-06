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
    incremental: isWatch,
    outfile: 'wireview/static/wireview/wireview.min.js',
    watch: isWatch,
};

esbuild.build(buildOptions);
