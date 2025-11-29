function drawCharts (labels,data) {
 "use strict";
	 /*----------------------------------------*/
	/*  1.  Bar Chart
	/*----------------------------------------*/
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
        return hex.replace('#', 'rgba(')  // simple hex to rgba fallback
    });
    // simple hex -> rgba conversion
    bgColors = labels.map(function(_, idx){
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
	var barchart1 = new Chart(ctx, {
		type: 'bar',
		data: {
			labels: labels,
			datasets: [{
				label: 'Amount',
				data: data,
				backgroundColor: bgColors,
				borderColor: borderColors,
				borderWidth: 1.5
			}]
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
					}
				}],
				xAxes: [{
					ticks: {
						fontColor: '#cbd5e1'
					},
					gridLines: {
						color: 'rgba(148, 163, 184, 0.2)'
					}
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
	/*----------------------------------------*/
	/*  2.  Bar Chart vertical
	/*----------------------------------------*/
	var ctx = document.getElementById("barchart2");
	var barchart2 = new Chart(ctx, {
		type: 'bar',
		data: {
			labels: ["January", "February"],
			datasets: [{
                label: 'Dataset 1',
				data: [150, 170],
				borderWidth: 1,
                backgroundColor: [
					'rgba(255, 99, 132, 0.2)',
					'rgb(50,205,50, 0.2)'
				],
				borderColor: [
					'rgba(255,99,132,1)',
					'rgba(54, 162, 235, 1)'
				],
            }, {
                label: 'Dataset 2',
				data: [-188, -177],
                backgroundColor: [
					'rgba(255, 99, 132, 0.2)',
					'rgb(50,205,50, 0.2)'
				],
				borderColor: [
					'rgba(255,99,132,1)',
					'rgba(54, 162, 235, 1)'
				],
				borderWidth: 1
            }]
		},
		options: {
			responsive: true,
			legend: {
				position: 'top',
			},
			title: {
				display: true,
				text: 'Bar Chart Vertical'
			}
		}
	});
	/*----------------------------------------*/
	/*  3.  Bar Chart Horizontal
	/*----------------------------------------*/
	var ctx = document.getElementById("barchart3");
	var barchart3 = new Chart(ctx, {
		type: 'horizontalBar',
		data: {
			labels: ["May", "June"],
			datasets: [{
                label: 'Dataset 1',
				data: [3, 9],
				borderWidth: 1,
                backgroundColor: [
					'rgba(255, 99, 132, 0.2)',
					'rgb(50,205,50, 0.2)'
				],
				borderColor: [
					'rgba(255,99,132,1)',
					'rgba(54, 162, 235, 1)'
				],
            }, {
                label: 'Dataset 2',
				data: [-9, -15],
                backgroundColor: [
					'rgba(255, 99, 132, 0.2)',
					'rgb(50,205,50, 0.2)'
				],
				borderColor: [
					'rgba(255,99,132,1)',
					'rgba(54, 162, 235, 1)'
				],
				borderWidth: 1
            }]
		},
		options: {
			responsive: true,
			legend: {
				position: 'top',
			},
			title: {
				display: true,
				text: 'Bar Chart horizontal'
			}
		}
	});
	
	/*----------------------------------------*/
	/*  4.  Bar Chart Multi axis
	/*----------------------------------------*/
	var ctx = document.getElementById("barchart4");
	var barchart4 = new Chart(ctx, {
		type: 'bar',
		data: {
			labels: ["March", "April"],
			datasets: [{
                label: 'Dataset 1',
				data: [12, 19, 3, 5, 2, 3, 9],
				borderWidth: 1,
				yAxisID: "y-axis-1",
                backgroundColor: [
					'rgba(255, 99, 132, 0.2)',
					'rgb(50,205,50, 0.2)',
					'rgba(255, 206, 86, 0.2)',
					'rgba(75, 192, 192, 0.2)',
					'rgba(153, 102, 255, 0.2)',
					'rgba(255, 159, 64, 0.2)'
				],
				borderColor: [
					'rgba(255,99,132,1)',
					'rgba(54, 162, 235, 1)',
					'rgba(255, 206, 86, 1)',
					'rgba(75, 192, 192, 1)',
					'rgba(153, 102, 255, 1)',
					'rgba(255, 159, 64, 1)'
				],
            }, {
                label: 'Dataset 2',
				data: [-3, -6, -5, -9, -15, -20],
				borderWidth: 1,
				yAxisID: "y-axis-2",
                backgroundColor: [
					'rgba(255, 99, 132, 0.2)',
					'rgb(50,205,50, 0.2)',
					'rgba(255, 206, 86, 0.2)',
					'rgba(75, 192, 192, 0.2)',
					'rgba(153, 102, 255, 0.2)',
					'rgba(255, 159, 64, 0.2)'
				],
				borderColor: [
					'rgba(255,99,132,1)',
					'rgba(54, 162, 235, 1)',
					'rgba(255, 206, 86, 1)',
					'rgba(75, 192, 192, 1)',
					'rgba(153, 102, 255, 1)',
					'rgba(255, 159, 64, 1)'
				],
				
            }]
		},
		options: {
			responsive: true,
			title:{
				display:true,
				text:"Bar Chart Multi Axis"
			},
			tooltips: {
				mode: 'index',
				intersect: true
			},
			scales: {
				yAxes: [{
					type: "linear",
					display: true,
					position: "left",
					id: "y-axis-1",
				}, {
					type: "linear",
					display: true,
					position: "right",
					id: "y-axis-2",
					gridLines: {
						drawOnChartArea: false
					}
				}],
			}
		}
	});
	
	
		
}(jQuery);
