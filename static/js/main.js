
console.log("JS is connected");

const app = Vue.createApp ({
    delimiters: ['{*', '*}'],
    data(){
        return {
            a: "Привет",
            value: 2,
        }
    },
    methods: {
        changeText () {
            this.a = 'Привет, мир!'
        },
        increment (value) {
            this.value = this.value + value
        }
    }
}).mount('#app');

const spans = Vue.createApp ({
    delimiters: ['{*', '*}'],
    data(){
        return {
            a: "Привет",
            value: 2,
        }
    },
    methods: {
        changeText () {
            this.a = 'Привет, мир!'
        },
        increment (value) {
            this.value = this.value + value
        }
    }
}).mount('.span');

console.log(app.a);
