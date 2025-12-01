function drawCharts(labels, data, shodanData, zoomeyeData) {
    "use strict";
    if (!Array.isArray(labels)) labels = [];
    if (!Array.isArray(data)) data = [];
    if (!Array.isArray(shodanData)) shodanData = [];
    if (!Array.isArray(zoomeyeData)) zoomeyeData = [];

    // Fallback when nothing to plot.
    if (!labels.length) {
        labels = ['No data'];
        data = [0];
        shodanData = [];
        zoomeyeData = [];
    }

    if (Chart && Chart.defaults && Chart.defaults.global) {
        Chart.defaults.global.defaultFontColor = '#cbd5e1';
        Chart.defaults.global.defaultFontFamily = "'Roboto', 'Helvetica Neue', 'Helvetica', 'Arial', sans-serif";
        Chart.defaults.global.defaultFontSize = 12;
    }

    var palette = [
        '#60A5FA', '#38BDF8', '#FACC15', '#34D399', '#F472B6', '#C084FC',
        '#F97316', '#22D3EE', '#A3E635', '#FDE047', '#4ADE80', '#FB7185',
        '#E879F9', '#2DD4BF', '#FBBF24', '#F59E0B'
    ];
    var bgColors = labels.map(function(_, idx){
        var hex = palette[idx % palette.length];
        var bigint = parseInt(hex.slice(1), 16);
        var r = (bigint >> 16) & 255;
        var g = (bigint >> 8) & 255;
        var b = bigint & 255;
        return 'rgba(' + r + ',' + g + ',' + b + ',0.85)';
    });
    var borderColors = labels.map(function(_, idx){
        var hex = palette[idx % palette.length];
        var bigint = parseInt(hex.slice(1), 16);
        var r = (bigint >> 16) & 255;
        var g = (bigint >> 8) & 255;
        var b = bigint & 255;
        return 'rgba(' + r + ',' + g + ',' + b + ',1)';
    });

    var ctx = document.getElementById("barchart1");
    if (!ctx) { return; }
    var datasets = [];
    var shodanSum = shodanData.reduce(function(a,b){ return a + (Number(b)||0); }, 0);
    var zoomeyeSum = zoomeyeData.reduce(function(a,b){ return a + (Number(b)||0); }, 0);
    // If Shodan is empty but aggregate data exists, use aggregate for Shodan.
    var shodanSeries = shodanData.length ? shodanData : labels.map(() => 0);
    if (shodanSum === 0 && data.length) {
        shodanSeries = data;
        shodanSum = shodanSeries.reduce(function(a,b){ return a + (Number(b)||0); }, 0);
    }
    var zoomeyeSeries = zoomeyeData.length ? zoomeyeData : labels.map(() => 0);

    datasets.push({
        label: 'Shodan',
        data: shodanSeries,
        backgroundColor: 'rgba(96,165,250,0.85)',
        borderColor: 'rgba(59,130,246,1)',
        borderWidth: 1.5,
        stack: 'providers'
    });
    datasets.push({
        label: 'ZoomEye',
        data: zoomeyeSeries,
        backgroundColor: 'rgba(248,113,113,0.85)',
        borderColor: 'rgba(239,68,68,1)',
        borderWidth: 1.5,
        stack: 'providers'
    });

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            scales: {
                yAxes: [{
                    ticks: {
                        beginAtZero:true,
                        fontColor: '#cbd5e1'
                    },
                    gridLines: {
                        color: 'rgba(148, 163, 184, 0.2)'
                    },
                    stacked: true
                }],
                xAxes: [{
                    ticks: {
                        fontColor: '#cbd5e1'
                    },
                    gridLines: {
                        color: 'rgba(148, 163, 184, 0.2)'
                    },
                    stacked: true
                }]
            },
            legend: {
                labels: { fontColor: '#e5e7eb' }
            },
            tooltips: {
                backgroundColor: '#111827',
                titleFontColor: '#e5e7eb',
                bodyFontColor: '#e5e7eb',
                borderColor: '#334155',
                borderWidth: 1
            }
        }
    });
}
